from datetime import UTC, datetime
from pathlib import Path

import pytest

from top_pypi_dependents.sources.fixture import FixtureSource

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def source() -> FixtureSource:
    return FixtureSource(FIXTURES)


def test_winners_are_loaded_with_canonical_names(source: FixtureSource) -> None:
    by_canonical = {w.canonical_name: w for w in source.winners()}
    assert by_canonical["zope-interface"].name == "zope.interface"
    assert by_canonical["ruamel-yaml"].name == "Ruamel_YAML"
    assert by_canonical["django"].version == "6.0.1"


def test_winner_upload_time_is_timezone_aware(source: FixtureSource) -> None:
    winner = next(w for w in source.winners() if w.canonical_name == "requests")
    assert winner.upload_time == datetime(2026, 5, 14, 19, 25, 27, tzinfo=UTC)


def test_requires_dist_is_a_tuple(source: FixtureSource) -> None:
    winner = next(w for w in source.winners() if w.canonical_name == "no-deps")
    assert winner.requires_dist == ()


def test_audit_sample_groups_versions_by_canonical_project(
    source: FixtureSource,
) -> None:
    sample = source.audit_sample()
    assert sorted(sample["requests"]) == ["2.34.1", "2.34.2", "3.0.0rc1"]
    assert sorted(sample["epochal"]) == ["1!0.1", "99.0"]


def test_live_names_are_canonical_and_exclude_deleted(source: FixtureSource) -> None:
    live = source.live_names()
    assert "django" in live
    assert "deleted-project" not in live
    assert "totally-not-on-pypi" not in live


def test_source_reports_its_name(source: FixtureSource) -> None:
    assert source.name == "fixture"
