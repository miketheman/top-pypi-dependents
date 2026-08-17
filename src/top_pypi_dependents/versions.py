"""Latest-release selection, and the oracle that keeps the SQL honest."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from packaging.version import InvalidVersion, Version

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence


def select_latest(versions: Iterable[str]) -> str | None:
    """Return the highest final release or highest prerelease if all are pre.

    Mirrors Warehouse's ``ORDER BY is_prerelease ASC, _pypi_ordering DESC``.
    Versions that ``packaging`` cannot parse are skipped.
    """
    parsed: list[tuple[Version, str]] = []
    for raw in versions:
        try:
            parsed.append((Version(raw), raw))
        except InvalidVersion:
            continue

    if not parsed:
        return None

    finals = [pair for pair in parsed if not pair[0].is_prerelease]
    pool = finals or parsed
    return max(pool, key=lambda pair: pair[0])[1]


@dataclass(frozen=True, slots=True)
class Disagreement:
    """One project where the SQL sort key and ``packaging`` chose differently."""

    project: str
    sql_pick: str | None
    packaging_pick: str | None


def _picks_agree(actual: str | None, expected: str | None) -> bool:
    """Compare two version picks, treating PEP 440-equal versions as equal."""
    if actual is not None and expected is not None:
        try:
            return Version(actual) == Version(expected)
        except InvalidVersion:
            pass
    return actual == expected


def audit(
    sample: Mapping[str, Sequence[str]],
    sql_picks: Mapping[str, str],
) -> list[Disagreement]:
    """Compare the extract query's winners against ``packaging`` over a sample.

    ``sample`` maps a canonical project name to every version it has published.
    ``sql_picks`` maps a canonical project name to the version the query chose.
    """
    found: list[Disagreement] = []
    for project, all_versions in sorted(sample.items()):
        expected = select_latest(all_versions)
        actual = sql_picks.get(project)
        if not _picks_agree(actual, expected):
            found.append(
                Disagreement(project=project, sql_pick=actual, packaging_pick=expected)
            )
    return found
