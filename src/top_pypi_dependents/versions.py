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


@dataclass(frozen=True, slots=True)
class AuditResult:
    """The outcome of comparing the extract query's winners against ``packaging``."""

    disagreements: list[Disagreement]
    skipped: int
    """Projects skipped because none of their versions are packaging-parseable.

    ``packaging`` has no oracle pick for these, so the SQL's pick (whatever it is)
    cannot be judged right or wrong; counting them keeps the audit's shrinking
    coverage visible instead of silently comparing against nothing.
    """


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
) -> AuditResult:
    """Compare the extract query's winners against ``packaging`` over a sample.

    ``sample`` maps a canonical project name to every version it has published.
    ``sql_picks`` maps a canonical project name to the version the query chose.

    Projects with no packaging-parseable version at all (setuptools-era strings
    like ``1.5dev-r649``) have no oracle pick to compare against and are skipped
    rather than reported as disagreements.
    """
    found: list[Disagreement] = []
    skipped = 0
    for project, all_versions in sorted(sample.items()):
        expected = select_latest(all_versions)
        if expected is None:
            skipped += 1
            continue
        actual = sql_picks.get(project)
        if not _picks_agree(actual, expected):
            found.append(
                Disagreement(project=project, sql_pick=actual, packaging_pick=expected)
            )
    return AuditResult(disagreements=found, skipped=skipped)
