"""Render the static site."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from importlib.resources import files
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, PackageLoader, select_autoescape

from top_pypi_dependents.graph import GRAPH_VERSION, decode_edges

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
# Where the named source can be read about. A source with no public
# documentation -- the fixture -- is named without a link rather than linked
# somewhere approximate.
_SOURCE_LINKS = {
    "bigquery": "https://docs.pypi.org/api/bigquery/#project-metadata-table",
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


def _versioned(path: str, content: bytes) -> str:
    """A URL for a file the pages load, carrying a hash of its content.

    A returning reader's cached copy is dropped exactly when the file changes
    rather than up to ten minutes later, when Pages' cache lets go: a page's
    markup, its script and the data the script reads, from different months,
    would not agree on ids or on fields.
    """
    return f"./{path}?v={hashlib.sha256(content).hexdigest()[:12]}"


def _write_assets(out_dir: Path) -> dict[str, str]:
    """Copy the stylesheets and scripts beside the pages; return their URLs."""
    target = out_dir / "assets"
    target.mkdir(parents=True, exist_ok=True)
    urls = {}
    sources = files("top_pypi_dependents").joinpath("assets").iterdir()
    for source in sorted(sources, key=lambda source: source.name):
        content = source.read_bytes()
        (target / source.name).write_bytes(content)
        urls[source.name] = _versioned(f"assets/{source.name}", content)
    return urls


def _importmap(assets: dict[str, str]) -> dict[str, dict[str, str]]:
    """Scripts import each other by plain relative path; this gives those
    imports the same content-hashed URLs the pages use."""
    return {
        "imports": {
            f"./assets/{name}": url
            for name, url in assets.items()
            if name.endswith(".js")
        }
    }


def _environment() -> Environment:
    return Environment(
        loader=PackageLoader("top_pypi_dependents", "templates"),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


class StaleGraphError(ValueError):
    """The graph cannot be published with this payload or these pages."""


def _read_graph(path: Path, payload: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    """The graph file's bytes, and what the cascade page says about it.

    Everything is read and checked here, before a single file is written: a
    graph in another format would break the page script published beside it,
    and one built from another month's snapshot would be published under this
    month's footer, with last month's ranks and counts.
    """
    content = path.read_bytes()
    data = json.loads(content)
    if not isinstance(data, dict):
        msg = f"{path} does not hold a graph; rebuild it with `graph`"
        raise StaleGraphError(msg)
    if data.get("version") != GRAPH_VERSION:
        msg = (
            f"{path} is graph format {data.get('version')}, and these pages read "
            f"format {GRAPH_VERSION}; rebuild it with `graph`"
        )
        raise StaleGraphError(msg)
    if data["generated_at"] != payload["generated_at"]:
        msg = (
            f"{path} was generated at {data['generated_at']}, but the payload "
            f"at {payload['generated_at']}; rebuild it with `graph` from the "
            f"same database"
        )
        raise StaleGraphError(msg)
    # The key's example of a trace stopping short: the top project's runtime
    # dependents, and how many of them clear the bar to be drawn.
    hub = None
    if data["names"]:
        drawn = sum(
            1
            for dependent, dependency in decode_edges(data["edges"])
            if dependency == 0 and dependent < data["ranked"]
        )
        hub = {
            "name": data["names"][0],
            "dependents": data["dependents"][0],
            "drawn": drawn,
        }
    return content, {
        # The graph's own threshold, which can differ from the ranking's and
        # counts extras: the drawn set is every project that clears it once
        # extras count.
        "graph_min_dependents": data["min_dependents"],
        "graph_url": _versioned("graph.json", content),
        "hub": hub,
    }


def render_site(
    payload: dict[str, Any],
    out_dir: Path,
    *,
    rows: int = ROWS,
    graph: Path | None = None,
) -> None:
    """Write the pages, the JSON copies and the search index into ``out_dir``.

    ``rows`` is how many ranked projects the page lists; the search index
    covers the rest. With a ``graph`` file, the cascade page is rendered too.

    The graph is read and checked before anything is written.
    """
    cascade = None if graph is None else _read_graph(graph, payload)

    out_dir.mkdir(parents=True, exist_ok=True)
    env = _environment()
    assets = _write_assets(out_dir)
    shared = {
        "generated_at": _readable_date(payload["generated_at"]),
        "source": _SOURCE_LABELS.get(payload["source"], payload["source"]),
        "source_url": _SOURCE_LINKS.get(payload["source"]),
        "project_count": payload["project_count"],
        "edge_count": payload["edge_count"],
        # The corpus counts above describe what was analyzed. Without these two
        # the footer reads as though the file holds a million rows, which it has
        # not since `min_dependents` started cutting the single-dependent tail.
        "row_count": len(payload["rows"]),
        "min_dependents": payload.get("counting", {}).get("min_dependents", 1),
        # Stated, and queried for, only where the payload was counted that way;
        # older payloads counted self-references and must not be described as
        # if they had not.
        "self_references_excluded": payload.get("counting", {}).get("self_references")
        == "excluded",
        # Release assets are named for the month they cover, so the example
        # queries derive it rather than hardcoding a month that goes stale on
        # the next run.
        "asset_month": _asset_month(payload["generated_at"]),
        "payload_shape": _payload_shape(payload),
        "has_cascade": cascade is not None,
        "asset": assets.__getitem__,
        "importmap": _importmap(assets),
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
    index = _search_index(payload).encode()
    (out_dir / "search-index.json").write_bytes(index)
    shared["search_index_url"] = _versioned("search-index.json", index)

    # The method, the limitations and the query examples, on their own page. A
    # reader who came for the graph may never look at the table, and a reader
    # who came for a rank should not have to scroll past a SQL block to get one.
    (out_dir / "data.html").write_text(
        env.get_template("data.html.j2").render(page="data", **shared),
        encoding="utf-8",
    )

    if cascade is None:
        # A site rendered earlier with a graph would otherwise keep a cascade
        # page the nav no longer links, drawing another snapshot's graph.
        for stale in ("cascade.html", "graph.json"):
            (out_dir / stale).unlink(missing_ok=True)
    else:
        # Copied verbatim: `graph` already wrote it compact, and the page is
        # what reads it.
        content, context = cascade
        (out_dir / "graph.json").write_bytes(content)
        (out_dir / "cascade.html").write_text(
            env.get_template("cascade.html.j2").render(
                page="cascade", **context, **shared
            ),
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
