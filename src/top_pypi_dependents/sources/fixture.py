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


def _parse_upload_time(value: object) -> datetime | None:
    """A missing key and an explicit JSON ``null`` both yield ``None``."""
    return None if value is None else datetime.fromisoformat(str(value))


class FixtureSource:
    """Reads ``winners.jsonl``, ``audit_sample.jsonl``, and ``live_names.txt``."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    @property
    def name(self) -> str:
        return "fixture"

    def winners(self) -> list[Winner]:
        rows = _read_jsonl(self._directory / "winners.jsonl")
        return [
            Winner(
                name=str(row["name"]),
                canonical_name=canonical(str(row["name"])),
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
