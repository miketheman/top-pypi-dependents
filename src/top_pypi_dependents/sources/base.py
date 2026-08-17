"""The contract every metadata source satisfies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True, slots=True)
class Winner:
    """The release of one project that the ranking reads dependencies from."""

    name: str
    canonical_name: str
    version: str
    upload_time: datetime | None
    requires_dist: tuple[str, ...]
    summary: str
    requires_python: str


@runtime_checkable
class MetadataSource(Protocol):
    """Supplies everything the build stage needs, from anywhere."""

    @property
    def name(self) -> str:
        """Identifier recorded in ``snapshots.source``."""
        ...

    def winners(self) -> list[Winner]:
        """One selected release per project."""
        ...

    def audit_sample(self) -> dict[str, list[str]]:
        """Canonical project name to every version it has published, for a subset."""
        ...

    def live_names(self) -> set[str]:
        """Canonical names of every project currently live on PyPI."""
        ...
