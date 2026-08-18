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


def _asset_month(generated_at: str) -> str:
    """``YYYY-MM``, which is how the monthly release names its assets."""
    try:
        moment = datetime.fromisoformat(generated_at).astimezone(UTC)
    except ValueError:
        return "YYYY-MM"
    return f"{moment:%Y-%m}"


def _payload_shape(payload: dict[str, Any]) -> str:
    """The published payload with a single row kept, as a shape to read.

    Generated from the payload being rendered rather than written by hand, so
    it cannot drift from what the site actually serves.
    """
    sample = {**payload, "rows": payload["rows"][:1]}
    return json.dumps(sample, indent=2)


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
    """Write ``index.html``, ``rankings.html`` and both JSON copies into ``out_dir``.

    ``tiers`` are the reveal steps on the rankings page, smallest shown first.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    steps = sorted(set(tiers))
    env = _environment()
    shared = {
        "generated_at": _readable_date(payload["generated_at"]),
        "source": _SOURCE_LABELS.get(payload["source"], payload["source"]),
        "project_count": payload["project_count"],
        "edge_count": payload["edge_count"],
        # The corpus counts above describe what was analysed. Without these two
        # the footer reads as though the file holds a million rows, which it has
        # not since `min_dependents` started cutting the single-dependent tail.
        "row_count": len(payload["rows"]),
        "min_dependents": payload.get("counting", {}).get("min_dependents", 1),
        # Release assets are named for the month they cover, so the example
        # queries derive it rather than hardcoding a month that goes stale on
        # the next run.
        "asset_month": _asset_month(payload["generated_at"]),
        "payload_shape": _payload_shape(payload),
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

    # One page, revealed in steps, rather than a page per tier. The largest step
    # bounds what the page carries; the smallest is what it shows on arrival.
    # Rows past that are `hidden` in the markup rather than hidden by script, so
    # the browser never lays them out -- which is the whole point, and would be
    # lost if 10,000 rows rendered before JavaScript cut them back.
    (out_dir / "rankings.html").write_text(
        env.get_template("table.html.j2").render(
            rows=payload["rows"][: steps[-1]], steps=steps, initial=steps[0], **shared
        ),
        encoding="utf-8",
    )
