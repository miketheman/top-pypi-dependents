"""Render the static site."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, PackageLoader, select_autoescape

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

TIERS: tuple[int, ...] = (100, 1000, 10000)

# The payload keeps the full ISO timestamp for machines. The page is read by
# people, for whom microseconds and a UTC offset are noise on a dataset that
# only moves once a month.
_SOURCE_LABELS = {
    "bigquery": "PyPI metadata on BigQuery",
    "fixture": "the checked-in fixture",
}


def _readable_date(generated_at: str) -> str:
    """Format the payload's ISO timestamp for a human, or pass it through."""
    try:
        moment = datetime.fromisoformat(generated_at).astimezone(UTC)
    except ValueError:
        return generated_at
    return f"{moment:%B} {moment.day}, {moment.year}"


def _environment() -> Environment:
    return Environment(
        loader=PackageLoader("top_pypi_dependents", "templates"),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_site(
    payload: dict[str, Any],
    out_dir: Path,
    *,
    tiers: Sequence[int] = TIERS,
) -> None:
    """Write ``index.html`` and one page per tier into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    env = _environment()
    shared = {
        "tiers": list(tiers),
        "generated_at": _readable_date(payload["generated_at"]),
        "source": _SOURCE_LABELS.get(payload["source"], payload["source"]),
        "project_count": payload["project_count"],
        "edge_count": payload["edge_count"],
    }

    # Served from Pages rather than linked out of the git repository: a raw-git
    # URL ties consumers to the commit history and to whatever `data/` happens
    # to hold, where the site is the thing this project actually publishes. The
    # indented copy is for reading, the minified one for fetching.
    (out_dir / "latest.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "latest.min.json").write_text(
        json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8"
    )

    (out_dir / "index.html").write_text(
        env.get_template("index.html.j2").render(**shared), encoding="utf-8"
    )
    table = env.get_template("table.html.j2")
    for tier in tiers:
        (out_dir / f"top-{tier}.html").write_text(
            table.render(tier=tier, rows=payload["rows"][:tier], **shared),
            encoding="utf-8",
        )
