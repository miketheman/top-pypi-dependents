"""Render the static site."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, PackageLoader, select_autoescape

if TYPE_CHECKING:
    from pathlib import Path

# What the page lists. It carried ten times this behind a reveal ladder, back
# when the filter could only search what was rendered; `search-index.json`
# answers for every ranked project now, so the rest was markup bought for
# scrolling nobody does.
ROWS: int = 1000

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


def _search_index(payload: dict[str, Any]) -> str:
    """Every ranked project as a positional array, for the page to search.

    The rankings page renders a slice of the payload; without this the projects
    past that slice -- the large majority of them -- cannot be looked up at all.
    Positional arrays rather than objects because repeating five keys tens of
    thousands of times roughly doubles a file the browser has to fetch.
    """
    projects = [
        [
            row["project"],
            row["rank"],
            row["dependents"],
            row["dependents_all"],
            row["rank_change"],
        ]
        for row in payload["rows"]
    ]
    index = {
        "generated_at": payload["generated_at"],
        "count": len(projects),
        "fields": [
            "project",
            "rank",
            "dependents",
            "dependents_all",
            "rank_change",
        ],
        "projects": projects,
    }
    return json.dumps(index, separators=(",", ":")) + "\n"


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
    rows: int = ROWS,
) -> None:
    """Write both pages, both JSON copies and the search index into ``out_dir``.

    ``rows`` is how many ranked projects the page lists; the search index
    covers the rest.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
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

    # Fetched by the rankings page only once a search runs, so it costs an
    # arriving reader nothing.
    (out_dir / "search-index.json").write_text(_search_index(payload), encoding="utf-8")

    # The method, the limitations and the query examples, on their own page. A
    # reader who came for the graph may never look at the table, and a reader
    # who came for a rank should not have to scroll past a SQL block to get one.
    (out_dir / "data.html").write_text(
        env.get_template("data.html.j2").render(page="data", **shared),
        encoding="utf-8",
    )

    # The ranking is the root: it is what the site is for, and a visitor who
    # lands on prose has to take a second step to reach the thing they came for.
    listed = payload["rows"][:rows]
    (out_dir / "index.html").write_text(
        env.get_template("index.html.j2").render(
            page="rankings",
            rows=listed,
            on_page=len(listed),
            # A first run has no month to compare against, so every row reads
            # `new` and the column carries no information at all. It comes back
            # by itself the month something moves.
            show_change=any(row["rank_change"] is not None for row in payload["rows"]),
            **shared,
        ),
        encoding="utf-8",
    )
