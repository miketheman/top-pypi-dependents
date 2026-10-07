from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from top_pypi_dependents import warehouse
from top_pypi_dependents.sources.fixture import FixtureSource

if TYPE_CHECKING:
    import duckdb

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def con_and_snapshot() -> tuple[duckdb.DuckDBPyConnection, int]:
    """The fixture corpus loaded and ranked, as one snapshot.

    The floors are lowered: the corpus is 16 projects, five orders of magnitude
    below what production refuses to publish.
    """
    con = warehouse.connect(None)
    warehouse.create_schema(con)
    snapshot_id = warehouse.load_snapshot(
        con,
        source=FixtureSource(FIXTURES),
        captured_at=datetime(2026, 9, 1, tzinfo=UTC),
        floors=warehouse.Floors(winners=1, live_names=1, audit_sample=1),
    ).snapshot_id
    warehouse.compute_rankings(con, snapshot_id)
    return con, snapshot_id
