"""A metadata source backed by checked-in JSONL, for tests and offline development."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING

from top_pypi_dependents.normalize import canonical
from top_pypi_dependents.sources.base import Winner

if TYPE_CHECKING:
    from pathlib import Path


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _canonical_name(row: dict[str, object]) -> str:
    """The row's own ``canonical_name``, or one derived when it has none."""
    value = row.get("canonical_name")
    return canonical(str(row["name"])) if value is None else str(value)


def _parse_upload_time(value: object) -> datetime | None:
    """A missing key and an explicit JSON ``null`` both yield ``None``."""
    return None if value is None else datetime.fromisoformat(str(value))


class FixtureSource:
    """Reads ``winners.jsonl``, ``audit_sample.jsonl``, and ``live_names.txt``."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    @property
    def name(self) -> str:
        """Where the data came from, which is not what format it is in.

        This class reads the JSONL layout, and `extract` writes exactly that
        layout from BigQuery -- so the class serves production too, and a run
        that reported "fixture" published a lie about its own provenance.
        `extract` records the truth in `source.txt`; the checked-in corpus has
        no such file and keeps the default.
        """
        recorded = self._directory / "source.txt"
        if recorded.exists():
            return recorded.read_text(encoding="utf-8").strip() or "fixture"
        return "fixture"

    def winners(self) -> list[Winner]:
        rows = _read_jsonl(self._directory / "winners.jsonl")
        return [
            Winner(
                name=str(row["name"]),
                # Carried through exactly as the extract computed it, so the
                # load stage's guard compares BigQuery's canonicalization
                # against `packaging`'s rather than against itself. Older
                # fixtures without the column fall back to deriving it.
                canonical_name=_canonical_name(row),
                version=str(row["version"]),
                upload_time=_parse_upload_time(row.get("upload_time")),
                requires_dist=tuple(str(item) for item in row["requires_dist"]),  # ty: ignore[not-iterable]
                summary=str(row["summary"]),
                requires_python=str(row["requires_python"]),
            )
            for row in rows
        ]

    def audit_sample(self) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = defaultdict(list)
        for row in _read_jsonl(self._directory / "audit_sample.jsonl"):
            grouped[canonical(str(row["name"]))].append(str(row["version"]))
        return dict(grouped)

    def live_names(self) -> set[str]:
        text = (self._directory / "live_names.txt").read_text(encoding="utf-8")
        return {canonical(line) for line in text.split() if line}
