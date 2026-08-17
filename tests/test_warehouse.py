import dataclasses
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from top_pypi_dependents import warehouse
from top_pypi_dependents.sources.base import Winner
from top_pypi_dependents.sources.fixture import FixtureSource

FIXTURES = Path(__file__).parent / "fixtures"
CAPTURED = datetime(2026, 9, 1, tzinfo=UTC)
# The fixture corpus is 16 projects with a 3-project audit sample; the production
# floors are five orders of magnitude above that, so every test load lowers them.
FLOORS = warehouse.Floors(winners=1, live_names=1, audit_sample=1)

ConAndSnapshot = tuple[duckdb.DuckDBPyConnection, int]


_ROW_COUNT_QUERIES = {
    "projects": "SELECT count(*) FROM projects",
    "dependencies": "SELECT count(*) FROM dependencies",
    "snapshots": "SELECT count(*) FROM snapshots",
    "rankings": "SELECT count(*) FROM rankings",
}


def _row_count(con: duckdb.DuckDBPyConnection, table: str) -> int:
    """Total row count in ``table``, across every snapshot."""
    result = con.execute(_ROW_COUNT_QUERIES[table]).fetchone()
    assert result is not None
    return result[0]


@pytest.fixture
def con_and_snapshot() -> ConAndSnapshot:
    con = warehouse.connect(None)
    warehouse.create_schema(con)
    snapshot_id = warehouse.load_snapshot(
        con, source=FixtureSource(FIXTURES), captured_at=CAPTURED, floors=FLOORS
    ).snapshot_id
    warehouse.compute_rankings(con, snapshot_id)
    return con, snapshot_id


def test_snapshot_row_records_provenance(con_and_snapshot: ConAndSnapshot) -> None:
    con, snapshot_id = con_and_snapshot
    row = con.execute(
        "SELECT source, captured_at, unparsed_count FROM snapshots "
        "WHERE snapshot_id = ?",
        [snapshot_id],
    ).fetchone()
    assert row is not None
    assert row[0] == "fixture"
    # DuckDB localizes TIMESTAMPTZ to the session timezone on fetch; the
    # instant is what matters, and datetime equality holds across offsets.
    assert row[1] == CAPTURED
    assert row[2] == 1  # malformed-deps has one unparseable entry


def test_is_live_reflects_the_simple_index(con_and_snapshot: ConAndSnapshot) -> None:
    con, snapshot_id = con_and_snapshot
    live = dict(
        con.execute(
            "SELECT canonical_name, is_live FROM projects WHERE snapshot_id = ?",
            [snapshot_id],
        ).fetchall()
    )
    assert live["django"] is True
    assert live["deleted-project"] is False


def test_edges_are_canonical_on_both_ends(con_and_snapshot: ConAndSnapshot) -> None:
    con, snapshot_id = con_and_snapshot
    rows = con.execute(
        "SELECT dependent, dependency FROM dependencies "
        "WHERE snapshot_id = ? AND dependent = 'ruamel-yaml'",
        [snapshot_id],
    ).fetchall()
    assert rows == [("ruamel-yaml", "requests")]


def test_extras_gated_edges_are_marked_not_runtime(
    con_and_snapshot: ConAndSnapshot,
) -> None:
    con, snapshot_id = con_and_snapshot
    rows = con.execute(
        "SELECT extra, is_runtime FROM dependencies "
        "WHERE snapshot_id = ? AND dependent = 'flask' AND dependency = 'pytest'",
        [snapshot_id],
    ).fetchall()
    assert sorted(rows) == [("dev", False), ("test", False)]


def test_unparseable_requirement_is_not_an_edge(
    con_and_snapshot: ConAndSnapshot,
) -> None:
    con, snapshot_id = con_and_snapshot
    result = con.execute(
        "SELECT count(*) FROM dependencies "
        "WHERE snapshot_id = ? AND dependent = 'malformed-deps'",
        [snapshot_id],
    ).fetchone()
    assert result is not None
    assert result[0] == 1


def test_duplicate_declarations_count_once(con_and_snapshot: ConAndSnapshot) -> None:
    con, snapshot_id = con_and_snapshot
    edges_result = con.execute(
        "SELECT count(*) FROM dependencies "
        "WHERE snapshot_id = ? AND dependent = 'dupe-decl' AND dependency = 'requests'",
        [snapshot_id],
    ).fetchone()
    dependents_result = con.execute(
        "SELECT dependents_runtime FROM rankings "
        "WHERE snapshot_id = ? AND canonical_name = 'requests'",
        [snapshot_id],
    ).fetchone()
    assert edges_result is not None
    assert dependents_result is not None
    assert edges_result[0] == 2
    # requests is depended on by: onlyprereleases, epochal, malformed-deps,
    # ghost-dep, dupe-decl (twice, counted once), ruamel-yaml.
    # deleted-project also declares it but is not live, so it does not count.
    assert dependents_result[0] == 6


def test_deleted_projects_neither_rank_nor_vote(
    con_and_snapshot: ConAndSnapshot,
) -> None:
    con, snapshot_id = con_and_snapshot
    ranked_result = con.execute(
        "SELECT count(*) FROM rankings "
        "WHERE snapshot_id = ? AND canonical_name = 'deleted-project'",
        [snapshot_id],
    ).fetchone()
    edge_result = con.execute(
        "SELECT count(*) FROM dependencies "
        "WHERE snapshot_id = ? AND dependent = 'deleted-project'",
        [snapshot_id],
    ).fetchone()
    assert ranked_result is not None
    assert edge_result is not None
    assert ranked_result[0] == 0
    assert edge_result[0] == 1  # the edge survives in the graph, it just does not count


def test_runtime_and_all_counts_differ_for_an_extras_only_target(
    con_and_snapshot: ConAndSnapshot,
) -> None:
    con, snapshot_id = con_and_snapshot
    row = con.execute(
        "SELECT dependents_runtime, dependents_all FROM rankings "
        "WHERE snapshot_id = ? AND canonical_name = 'pytest'",
        [snapshot_id],
    ).fetchone()
    assert row == (0, 1)


def test_non_pypi_targets_are_stored_but_not_ranked(
    con_and_snapshot: ConAndSnapshot,
) -> None:
    con, snapshot_id = con_and_snapshot
    edge_result = con.execute(
        "SELECT count(*) FROM dependencies "
        "WHERE snapshot_id = ? AND dependency = 'totally-not-on-pypi'",
        [snapshot_id],
    ).fetchone()
    ranked_result = con.execute(
        "SELECT count(*) FROM rankings "
        "WHERE snapshot_id = ? AND canonical_name = 'totally-not-on-pypi'",
        [snapshot_id],
    ).fetchone()
    assert edge_result is not None
    assert ranked_result is not None
    assert edge_result[0] == 1
    assert ranked_result[0] == 0


def test_ranks_are_dense_and_tie_broken_by_name(
    con_and_snapshot: ConAndSnapshot,
) -> None:
    con, snapshot_id = con_and_snapshot
    rows = con.execute(
        "SELECT canonical_name, rank_runtime FROM rankings "
        "WHERE snapshot_id = ? ORDER BY rank_runtime LIMIT 3",
        [snapshot_id],
    ).fetchall()
    # django and urllib3 are tied at 1 runtime dependent each (postal -> django,
    # requests -> urllib3); the tie is broken by canonical_name ascending.
    assert rows == [("requests", 1), ("django", 2), ("urllib3", 3)]


def _empty_warehouse() -> duckdb.DuckDBPyConnection:
    con = warehouse.connect(None)
    warehouse.create_schema(con)
    return con


def _assert_nothing_written(con: duckdb.DuckDBPyConnection) -> None:
    assert _row_count(con, "projects") == 0
    assert _row_count(con, "dependencies") == 0
    assert _row_count(con, "snapshots") == 0


def test_production_floors_reject_the_fixture_corpus() -> None:
    """The shipped defaults are what an unattended run gets when nobody passes."""
    con = _empty_warehouse()
    with pytest.raises(warehouse.ImplausibleRunError, match="winners"):
        warehouse.load_snapshot(
            con, source=FixtureSource(FIXTURES), captured_at=CAPTURED
        )
    _assert_nothing_written(con)


def test_too_few_winners_aborts_the_load() -> None:
    con = _empty_warehouse()
    floors = dataclasses.replace(FLOORS, winners=17)
    with pytest.raises(
        warehouse.ImplausibleRunError, match="16, below the floor of 17"
    ):
        warehouse.load_snapshot(
            con, source=FixtureSource(FIXTURES), captured_at=CAPTURED, floors=floors
        )
    _assert_nothing_written(con)


def test_a_truncated_simple_index_aborts_the_load() -> None:
    """The collapsed-liveness case: /simple/ answers, but with almost nothing."""

    class TruncatedIndexSource(FixtureSource):
        def live_names(self) -> set[str]:
            return {"requests"}

    con = _empty_warehouse()
    floors = dataclasses.replace(FLOORS, live_names=20)
    with pytest.raises(warehouse.ImplausibleRunError, match="live names"):
        warehouse.load_snapshot(
            con,
            source=TruncatedIndexSource(FIXTURES),
            captured_at=CAPTURED,
            floors=floors,
        )
    _assert_nothing_written(con)


def test_an_empty_audit_sample_no_longer_passes_vacuously() -> None:
    class UnauditedSource(FixtureSource):
        def audit_sample(self) -> dict[str, list[str]]:
            return {}

    con = _empty_warehouse()
    with pytest.raises(warehouse.ImplausibleRunError, match="audit sample"):
        warehouse.load_snapshot(
            con, source=UnauditedSource(FIXTURES), captured_at=CAPTURED, floors=FLOORS
        )
    _assert_nothing_written(con)


def test_a_mostly_skipped_audit_sample_aborts_the_load() -> None:
    class UnparseableSampleSource(FixtureSource):
        """Only one of four sampled projects has a version packaging can read."""

        def audit_sample(self) -> dict[str, list[str]]:
            return {
                "requests": ["2.34.2"],
                "ancient-one": ["1.5dev-r649"],
                "ancient-two": ["not a version"],
                "ancient-three": ["nor is this"],
            }

    con = _empty_warehouse()
    with pytest.raises(warehouse.ImplausibleRunError, match="skipped 3 of 4"):
        warehouse.load_snapshot(
            con,
            source=UnparseableSampleSource(FIXTURES),
            captured_at=CAPTURED,
            floors=FLOORS,
        )
    _assert_nothing_written(con)


def test_audit_disagreement_aborts_the_load() -> None:
    class LyingSource(FixtureSource):
        """Picks the prerelease that packaging says should lose."""

        def winners(self) -> list[Winner]:
            kept = [w for w in super().winners() if w.canonical_name != "requests"]
            return [
                *kept,
                Winner(
                    name="requests",
                    canonical_name="requests",
                    version="3.0.0rc1",
                    upload_time=datetime(2026, 5, 14, tzinfo=UTC),
                    requires_dist=(),
                    summary="",
                    requires_python="",
                ),
            ]

    con = warehouse.connect(None)
    warehouse.create_schema(con)
    with pytest.raises(warehouse.AuditFailedError) as excinfo:
        warehouse.load_snapshot(
            con, source=LyingSource(FIXTURES), captured_at=CAPTURED, floors=FLOORS
        )
    assert excinfo.value.disagreements[0].project == "requests"
    assert excinfo.value.disagreements[0].sql_pick == "3.0.0rc1"
    assert excinfo.value.disagreements[0].packaging_pick == "2.34.2"
    # The audit guard runs before the first write.
    assert _row_count(con, "projects") == 0
    assert _row_count(con, "dependencies") == 0
    assert _row_count(con, "snapshots") == 0


def test_canonicalization_mismatch_raises_before_writing() -> None:
    class MisnamingSource(FixtureSource):
        """Claims a canonical name that ``packaging`` would not produce."""

        def winners(self) -> list[Winner]:
            kept = [w for w in super().winners() if w.canonical_name != "requests"]
            liar = next(w for w in super().winners() if w.canonical_name == "requests")
            return [*kept, dataclasses.replace(liar, canonical_name="not-requests")]

        def audit_sample(self) -> dict[str, list[str]]:
            # Drop the tampered project so the version-selection audit passes
            # and the canonicalization re-derivation guard is the one under test.
            return {
                name: versions
                for name, versions in super().audit_sample().items()
                if name != "requests"
            }

    con = warehouse.connect(None)
    warehouse.create_schema(con)
    with pytest.raises(ValueError, match="not-requests"):
        warehouse.load_snapshot(
            con, source=MisnamingSource(FIXTURES), captured_at=CAPTURED, floors=FLOORS
        )
    assert _row_count(con, "projects") == 0
    assert _row_count(con, "dependencies") == 0
    assert _row_count(con, "snapshots") == 0


def test_snapshot_with_no_parseable_edges_skips_the_dependencies_insert() -> None:
    class NoEdgesSource(FixtureSource):
        """A snapshot whose only project declares no dependencies."""

        def winners(self) -> list[Winner]:
            return [w for w in super().winners() if w.canonical_name == "no-deps"]

        def audit_sample(self) -> dict[str, list[str]]:
            # The sample has to cover projects this source actually returns, or
            # every sampled project reads as a version-selection disagreement.
            return {"no-deps": ["5.0.0", "4.9.0"]}

    con = warehouse.connect(None)
    warehouse.create_schema(con)
    load_result = warehouse.load_snapshot(
        con, source=NoEdgesSource(FIXTURES), captured_at=CAPTURED, floors=FLOORS
    )
    assert _row_count(con, "projects") == 1
    assert _row_count(con, "dependencies") == 0
    assert load_result.snapshot_id == 1


class _FailAfterFirstExecuteMany:
    """Wraps a real connection so the second ``executemany`` call raises.

    ``load_snapshot`` makes exactly two ``executemany`` calls when a snapshot
    has edges: one to insert ``projects``, one to insert ``dependencies``. This
    fails the second, simulating a crash between them.
    """

    def __init__(self, real: duckdb.DuckDBPyConnection) -> None:
        self._real = real
        self._executemany_calls = 0

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)

    def executemany(
        self, query: str, parameters: object = None
    ) -> duckdb.DuckDBPyConnection:
        self._executemany_calls += 1
        if self._executemany_calls == 2:
            msg = "simulated failure between the projects and dependencies inserts"
            raise RuntimeError(msg)
        return self._real.executemany(query, parameters)


def test_load_snapshot_rolls_back_a_mid_load_failure() -> None:
    con = warehouse.connect(None)
    warehouse.create_schema(con)
    wrapped = _FailAfterFirstExecuteMany(con)

    with pytest.raises(RuntimeError, match="simulated failure"):
        warehouse.load_snapshot(
            wrapped,  # ty: ignore[invalid-argument-type]
            source=FixtureSource(FIXTURES),
            captured_at=CAPTURED,
            floors=FLOORS,
        )

    assert _row_count(con, "projects") == 0
    assert _row_count(con, "dependencies") == 0
    assert _row_count(con, "snapshots") == 0

    # A retry starts clean instead of merging into an orphaned snapshot_id.
    snapshot_id = warehouse.load_snapshot(
        con, source=FixtureSource(FIXTURES), captured_at=CAPTURED, floors=FLOORS
    ).snapshot_id
    assert snapshot_id == 1
    requests_rows = con.execute(
        "SELECT count(*) FROM projects "
        "WHERE snapshot_id = ? AND canonical_name = 'requests'",
        [snapshot_id],
    ).fetchone()
    assert requests_rows is not None
    assert requests_rows[0] == 1


def test_null_upload_time_survives_as_null(con_and_snapshot: ConAndSnapshot) -> None:
    con, snapshot_id = con_and_snapshot
    row = con.execute(
        "SELECT latest_upload_time FROM projects "
        "WHERE snapshot_id = ? AND canonical_name = 'null-upload-time'",
        [snapshot_id],
    ).fetchone()
    assert row is not None
    assert row[0] is None


def test_compute_rankings_is_idempotent(con_and_snapshot: ConAndSnapshot) -> None:
    con, snapshot_id = con_and_snapshot
    before = con.execute(
        "SELECT count(*) FROM rankings WHERE snapshot_id = ?", [snapshot_id]
    ).fetchone()
    warehouse.compute_rankings(con, snapshot_id)
    after = con.execute(
        "SELECT count(*) FROM rankings WHERE snapshot_id = ?", [snapshot_id]
    ).fetchone()
    assert before is not None
    assert after is not None
    assert before == after
