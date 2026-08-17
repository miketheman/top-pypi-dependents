"""PEP 503 name normalization and PEP 508 requirement parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

_EXTRA_GATE = re.compile(r"""extra\s*==\s*(?P<q>["'])(?P<name>[^"']+)(?P=q)""")


def canonical(name: str) -> str:
    """Normalize a project name per PEP 503."""
    return str(canonicalize_name(name))


def gates(marker_text: str | None) -> tuple[str, ...]:
    """Return the sorted, deduplicated ``extra == "..."`` names in a marker."""
    if not marker_text:
        return ()
    return tuple(sorted({m.group("name") for m in _EXTRA_GATE.finditer(marker_text)}))


@dataclass(frozen=True, slots=True)
class ParsedRequirement:
    """One declared dependency edge, decomposed."""

    dependency: str
    dependency_raw: str
    specifier: str
    extra: str | None
    marker: str | None
    is_runtime: bool


def parse_requirement(text: str) -> ParsedRequirement | None:
    """Parse one ``requires_dist`` entry, or return ``None`` if it is malformed."""
    try:
        req = Requirement(text)
    except InvalidRequirement:
        return None

    marker = str(req.marker) if req.marker is not None else None
    found = gates(marker)
    return ParsedRequirement(
        dependency=canonical(req.name),
        dependency_raw=req.name,
        specifier=str(req.specifier),
        extra=",".join(found) if found else None,
        marker=marker,
        is_runtime=not found,
    )
